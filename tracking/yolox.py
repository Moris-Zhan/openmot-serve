# Derived from YOLOX (https://github.com/Megvii-BaseDetection/YOLOX), commit
# 6ddff4824372906469a7fae2dc3206c7aa4bbaee.
# Copyright (c) Megvii Inc. All rights reserved.
# Licensed under the Apache License, Version 2.0; the full text is in
# tracking/licenses/YOLOX-LICENSE.
#
# Modifications: inference-only YOLOX-X (training, losses, label assignment,
# depthwise variants and non-SiLU activations removed); head outputs are decoded in
# float32; weight loading verifies a SHA-256 and uses torch.load(weights_only=True).

import hashlib
from pathlib import Path

import torch
import torch.nn as nn

WEIGHTS_URL = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_x.pth"
)
WEIGHTS_SHA256 = "5652330b6ae860043f091b8f550a60c10e1129f416edfdb65c259be6caf355cf"
WEIGHTS_ID = "yolox_x.pth@0.1.1rc0"

CLASS_COUNT = 80
STRIDES = (8, 16, 32)
DEPTH_MULTIPLIER = 1.33
WIDTH_MULTIPLIER = 1.25
BATCH_NORM_EPS = 1e-3
BATCH_NORM_MOMENTUM = 0.03


class WeightsIntegrityError(RuntimeError):
    pass


class BaseConv(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, ksize: int, stride: int) -> None:
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size=ksize,
            stride=stride,
            padding=(ksize - 1) // 2,
            bias=False,
        )
        self.bn = nn.BatchNorm2d(out_channels)
        self.act = nn.SiLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(self.bn(self.conv(x)))


class Bottleneck(nn.Module):
    def __init__(self, channels: int, shortcut: bool) -> None:
        super().__init__()
        self.conv1 = BaseConv(channels, channels, 1, 1)
        self.conv2 = BaseConv(channels, channels, 3, 1)
        self.use_add = shortcut

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.conv2(self.conv1(x))
        return y + x if self.use_add else y


class SPPBottleneck(nn.Module):
    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        hidden = in_channels // 2
        self.conv1 = BaseConv(in_channels, hidden, 1, 1)
        self.m = nn.ModuleList(
            [nn.MaxPool2d(kernel_size=k, stride=1, padding=k // 2) for k in (5, 9, 13)]
        )
        self.conv2 = BaseConv(hidden * 4, out_channels, 1, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv1(x)
        return self.conv2(torch.cat([x] + [pool(x) for pool in self.m], dim=1))


class CSPLayer(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, n: int, shortcut: bool = True) -> None:
        super().__init__()
        hidden = int(out_channels * 0.5)
        self.conv1 = BaseConv(in_channels, hidden, 1, 1)
        self.conv2 = BaseConv(in_channels, hidden, 1, 1)
        self.conv3 = BaseConv(2 * hidden, out_channels, 1, 1)
        self.m = nn.Sequential(*[Bottleneck(hidden, shortcut) for _ in range(n)])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        left = self.m(self.conv1(x))
        right = self.conv2(x)
        return self.conv3(torch.cat((left, right), dim=1))


class Focus(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, ksize: int) -> None:
        super().__init__()
        self.conv = BaseConv(in_channels * 4, out_channels, ksize, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        top_left = x[..., ::2, ::2]
        top_right = x[..., ::2, 1::2]
        bottom_left = x[..., 1::2, ::2]
        bottom_right = x[..., 1::2, 1::2]
        return self.conv(torch.cat((top_left, bottom_left, top_right, bottom_right), dim=1))


class CSPDarknet(nn.Module):
    def __init__(self, depth_multiplier: float, width_multiplier: float) -> None:
        super().__init__()
        base_channels = int(width_multiplier * 64)
        base_depth = max(round(depth_multiplier * 3), 1)

        self.stem = Focus(3, base_channels, ksize=3)
        self.dark2 = nn.Sequential(
            BaseConv(base_channels, base_channels * 2, 3, 2),
            CSPLayer(base_channels * 2, base_channels * 2, n=base_depth),
        )
        self.dark3 = nn.Sequential(
            BaseConv(base_channels * 2, base_channels * 4, 3, 2),
            CSPLayer(base_channels * 4, base_channels * 4, n=base_depth * 3),
        )
        self.dark4 = nn.Sequential(
            BaseConv(base_channels * 4, base_channels * 8, 3, 2),
            CSPLayer(base_channels * 8, base_channels * 8, n=base_depth * 3),
        )
        self.dark5 = nn.Sequential(
            BaseConv(base_channels * 8, base_channels * 16, 3, 2),
            SPPBottleneck(base_channels * 16, base_channels * 16),
            CSPLayer(base_channels * 16, base_channels * 16, n=base_depth, shortcut=False),
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x = self.stem(x)
        x = self.dark2(x)
        dark3 = self.dark3(x)
        dark4 = self.dark4(dark3)
        dark5 = self.dark5(dark4)
        return dark3, dark4, dark5


class YOLOPAFPN(nn.Module):
    def __init__(self, depth_multiplier: float, width_multiplier: float) -> None:
        super().__init__()
        channels = [int(c * width_multiplier) for c in (256, 512, 1024)]
        depth = round(3 * depth_multiplier)
        self.backbone = CSPDarknet(depth_multiplier, width_multiplier)

        self.upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.lateral_conv0 = BaseConv(channels[2], channels[1], 1, 1)
        self.C3_p4 = CSPLayer(2 * channels[1], channels[1], depth, False)
        self.reduce_conv1 = BaseConv(channels[1], channels[0], 1, 1)
        self.C3_p3 = CSPLayer(2 * channels[0], channels[0], depth, False)
        self.bu_conv2 = BaseConv(channels[0], channels[0], 3, 2)
        self.C3_n3 = CSPLayer(2 * channels[0], channels[1], depth, False)
        self.bu_conv1 = BaseConv(channels[1], channels[1], 3, 2)
        self.C3_n4 = CSPLayer(2 * channels[1], channels[2], depth, False)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x2, x1, x0 = self.backbone(x)

        fpn_out0 = self.lateral_conv0(x0)
        f_out0 = self.C3_p4(torch.cat([self.upsample(fpn_out0), x1], 1))

        fpn_out1 = self.reduce_conv1(f_out0)
        pan_out2 = self.C3_p3(torch.cat([self.upsample(fpn_out1), x2], 1))

        pan_out1 = self.C3_n3(torch.cat([self.bu_conv2(pan_out2), fpn_out1], 1))
        pan_out0 = self.C3_n4(torch.cat([self.bu_conv1(pan_out1), fpn_out0], 1))
        return pan_out2, pan_out1, pan_out0


class YOLOXHead(nn.Module):
    def __init__(self, class_count: int, width_multiplier: float) -> None:
        super().__init__()
        in_channels = [int(c * width_multiplier) for c in (256, 512, 1024)]
        hidden = int(256 * width_multiplier)
        self.strides = STRIDES

        self.stems = nn.ModuleList([BaseConv(c, hidden, 1, 1) for c in in_channels])
        self.cls_convs = nn.ModuleList(
            [nn.Sequential(BaseConv(hidden, hidden, 3, 1), BaseConv(hidden, hidden, 3, 1)) for _ in in_channels]
        )
        self.reg_convs = nn.ModuleList(
            [nn.Sequential(BaseConv(hidden, hidden, 3, 1), BaseConv(hidden, hidden, 3, 1)) for _ in in_channels]
        )
        self.cls_preds = nn.ModuleList([nn.Conv2d(hidden, class_count, 1) for _ in in_channels])
        self.reg_preds = nn.ModuleList([nn.Conv2d(hidden, 4, 1) for _ in in_channels])
        self.obj_preds = nn.ModuleList([nn.Conv2d(hidden, 1, 1) for _ in in_channels])

    def forward(self, features: tuple[torch.Tensor, ...]) -> torch.Tensor:
        raw_levels = []
        for level, feature in enumerate(features):
            stem = self.stems[level](feature)
            cls_output = self.cls_preds[level](self.cls_convs[level](stem))
            reg_feature = self.reg_convs[level](stem)
            reg_output = self.reg_preds[level](reg_feature)
            obj_output = self.obj_preds[level](reg_feature)
            raw_levels.append(
                torch.cat([reg_output, obj_output.sigmoid(), cls_output.sigmoid()], 1)
            )

        # Decode in float32: float16 has only 1 px resolution above 1024.
        flattened = torch.cat([level.flatten(start_dim=2) for level in raw_levels], dim=2)
        flattened = flattened.permute(0, 2, 1).float()
        return self._decode(flattened, [level.shape[-2:] for level in raw_levels])

    def _decode(self, outputs: torch.Tensor, grid_sizes: list[torch.Size]) -> torch.Tensor:
        grids = []
        strides = []
        for (height, width), stride in zip(grid_sizes, self.strides):
            rows, columns = torch.meshgrid(
                torch.arange(height, device=outputs.device),
                torch.arange(width, device=outputs.device),
                indexing="ij",
            )
            grids.append(torch.stack((columns, rows), 2).view(1, -1, 2))
            strides.append(torch.full((1, height * width, 1), stride, device=outputs.device))
        grid = torch.cat(grids, dim=1).float()
        stride = torch.cat(strides, dim=1).float()
        return torch.cat(
            [
                (outputs[..., 0:2] + grid) * stride,
                torch.exp(outputs[..., 2:4]) * stride,
                outputs[..., 4:],
            ],
            dim=-1,
        )


class YOLOX(nn.Module):
    def __init__(self, backbone: YOLOPAFPN, head: YOLOXHead) -> None:
        super().__init__()
        self.backbone = backbone
        self.head = head

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.backbone(x))


def grid_count(height: int, width: int) -> int:
    return sum((height // stride) * (width // stride) for stride in STRIDES)


def build_yolox_x() -> YOLOX:
    model = YOLOX(
        YOLOPAFPN(DEPTH_MULTIPLIER, WIDTH_MULTIPLIER),
        YOLOXHead(CLASS_COUNT, WIDTH_MULTIPLIER),
    )
    for module in model.modules():
        if isinstance(module, nn.BatchNorm2d):
            module.eps = BATCH_NORM_EPS
            module.momentum = BATCH_NORM_MOMENTUM
    return model


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_yolox_x(weights_path: Path, device: str) -> YOLOX:
    actual = sha256_of_file(weights_path)
    if actual != WEIGHTS_SHA256:
        raise WeightsIntegrityError(
            f"{weights_path} has SHA-256 {actual}, expected {WEIGHTS_SHA256} ({WEIGHTS_ID})"
        )
    checkpoint = torch.load(weights_path, map_location="cpu", weights_only=True)
    model = build_yolox_x()
    model.load_state_dict(checkpoint["model"])
    return model.eval().to(device)
