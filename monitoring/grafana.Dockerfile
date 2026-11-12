FROM grafana/grafana:11.4.0

COPY monitoring/grafana/provisioning /etc/grafana/provisioning
COPY monitoring/grafana/dashboards /var/lib/grafana/dashboards
