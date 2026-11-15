package cd.upc.mri.benchmark;

import org.springframework.http.ResponseEntity;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.NoHandlerFoundException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

/**
 * Remplace les erreurs par défaut de Spring (page Whitelabel, /error, ProblemDetail) par le
 * format unique {"error": "..."} : route ou méthode inconnue -> 404, le reste -> 500 (§5.6).
 */
@RestControllerAdvice
public class ApiErrors {

    private final Json json;

    public ApiErrors(Json json) {
        this.json = json;
    }

    @ExceptionHandler({NoHandlerFoundException.class, NoResourceFoundException.class,
            HttpRequestMethodNotSupportedException.class})
    public ResponseEntity<byte[]> notFound() {
        return json.error(404, "not_found");
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<byte[]> internal() {
        return json.error(500, "internal_error");
    }
}
