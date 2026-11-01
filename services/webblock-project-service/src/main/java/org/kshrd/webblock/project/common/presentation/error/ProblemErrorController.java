package org.kshrd.webblock.project.common.presentation.error;

import jakarta.servlet.RequestDispatcher;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.webmvc.error.ErrorController;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Receives the servlet container's ERROR dispatch (firewall rejections, errors raised outside Spring MVC) and writes the
 * Problem Details body with a status-based code. The exception message is never echoed; {@code instance} is the
 * original request's path.
 */
@RestController
public class ProblemErrorController implements ErrorController {

    private static final Logger log = LoggerFactory.getLogger(ProblemErrorController.class);

    private final ProblemDetailWriter problemDetailWriter;

    public ProblemErrorController(ProblemDetailWriter problemDetailWriter) {
        this.problemDetailWriter = problemDetailWriter;
    }

    @RequestMapping("${server.error.path:${error.path:/error}}")
    public void error(HttpServletRequest request, HttpServletResponse response) throws IOException {
        int status = statusOf(request);
        ErrorCode code = StatusErrorCodes.forStatus(status);
        if (code == ErrorCode.INTERNAL_ERROR) {
            log.error(
                    "Container-level error, status={}, path={}",
                    status,
                    ProblemDetailFactory.instance(request),
                    request.getAttribute(RequestDispatcher.ERROR_EXCEPTION) instanceof Throwable t ? t : null);
        }
        problemDetailWriter.write(request, response, code);
    }

    private static int statusOf(HttpServletRequest request) {
        return request.getAttribute(RequestDispatcher.ERROR_STATUS_CODE) instanceof Integer status
                ? status
                : HttpServletResponse.SC_INTERNAL_SERVER_ERROR;
    }
}
