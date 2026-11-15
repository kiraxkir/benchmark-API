package cd.upc.mri.benchmark;

import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Lecture et écriture JSON. La réponse est sérialisée ici en octets avec un Content-Type fixe
 * "application/json" : pas de négociation de contenu (en-tête Accept ignoré, comme ailleurs).
 */
@Component
public class Json {

    private final JsonMapper mapper = JsonMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();

    /** Objet JSON attendu (UTF-8 strict), sinon null (§5.6.4). */
    public JsonNode parseObject(byte[] raw) {
        if (raw == null || raw.length == 0) {
            return null;
        }
        try {
            JsonNode node = mapper.readTree(raw);
            return (node != null && node.isObject()) ? node : null;
        } catch (JacksonException e) {
            return null;
        }
    }

    public ResponseEntity<byte[]> ok(int status, Object body) {
        return ResponseEntity.status(status)
                .contentType(MediaType.APPLICATION_JSON)
                .body(mapper.writeValueAsBytes(body));
    }

    /** Format d'erreur unique du contrat : {"error": "<code>"}. */
    public ResponseEntity<byte[]> error(int status, String code) {
        return ok(status, Map.of("error", code));
    }
}
