# Image de production Spring Boot (Tomcat embarqué), build Maven multi-étapes (CLAUDE.md §7).

# --- Étape 1 : compilation (dépendances téléchargées dans une couche séparée) ---
FROM maven:3.9-eclipse-temurin-25 AS build
WORKDIR /src
COPY pom.xml .
RUN mvn -B -q dependency:go-offline
COPY src ./src
RUN mvn -B -q -o -DskipTests package && cp target/benchmark-api-*.jar /app.jar

# --- Étape 2 : exécution ---
FROM eclipse-temurin:25-jre
WORKDIR /app
COPY --from=build /app.jar app.jar
EXPOSE 8000
ENTRYPOINT ["java", "-Xmx512m", "-jar", "app.jar"]
