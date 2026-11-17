# syntax=docker/dockerfile:1
FROM node:22-slim AS deps
WORKDIR /srv
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN npm ci --workspaces --include-workspace-root

FROM deps AS build
COPY packages/shared-types packages/shared-types
COPY apps/web apps/web
ARG APEX_API_ORIGIN=http://api:8000
ARG NEXT_PUBLIC_APEX_WS_URL=ws://localhost:8000/api/ws
ENV APEX_API_ORIGIN=$APEX_API_ORIGIN NEXT_PUBLIC_APEX_WS_URL=$NEXT_PUBLIC_APEX_WS_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build -w web

FROM node:22-slim AS run
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /srv
COPY --from=build /srv /srv
USER node
EXPOSE 3000
CMD ["npm", "run", "start", "-w", "web", "--", "--port", "3000", "--hostname", "0.0.0.0"]
