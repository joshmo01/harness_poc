FROM node:22-slim
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npx vite build
EXPOSE 5173
CMD ["npx", "vite", "preview", "--host", "0.0.0.0"]
