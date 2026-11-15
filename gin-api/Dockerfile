# Image de production Gin, build Go multi-étapes (CLAUDE.md §7).

# --- Étape 1 : compilation (binaire statique) ---
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /gin-api .

# --- Étape 2 : exécution ---
FROM alpine:3.22
COPY --from=build /gin-api /usr/local/bin/gin-api
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/gin-api"]
