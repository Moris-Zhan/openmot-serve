FROM prom/prometheus:v2.55.1

COPY monitoring/prometheus.yml /etc/prometheus/prometheus.yml
COPY monitoring/rules /etc/prometheus/rules
