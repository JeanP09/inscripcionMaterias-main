# Etapa 1: build de la SPA con Vite
FROM node:20-alpine AS build
WORKDIR /app

# VITE_API_URL vacío => el frontend usará rutas relativas /api y Nginx hará
# el proxy al servicio "backend". Se puede sobreescribir en docker-compose.
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL

COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Etapa 2: servir estáticos con Nginx
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
