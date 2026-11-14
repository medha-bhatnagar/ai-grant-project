FROM nginx:1.27-alpine

# nginx fills in ${DASHBOARD_PORT} and ${JUDGE_PORT} from compose.yaml when it starts.
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY index.html /usr/share/nginx/html/index.html
