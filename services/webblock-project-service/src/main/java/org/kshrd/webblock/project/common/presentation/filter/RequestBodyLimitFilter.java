package org.kshrd.webblock.project.common.presentation.filter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.presentation.error.BodyTooLargeException;
import org.kshrd.webblock.project.common.presentation.error.ProblemDetailWriter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Caps the size of a request body ({@code webblock.http.max-body-bytes}, default 1.5 MiB: the Puck tree is at most 1 MB).
 * It runs right after {@link RequestIdFilter} and before Spring Security on purpose: an oversized body is refused
 * before a token is checked or any of the body is parsed, so an unauthenticated caller cannot make the service read it.
 * A declared {@code Content-Length} over the limit is refused at once; for a chunked body the stream is counted and
 * stops at the limit.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class RequestBodyLimitFilter extends OncePerRequestFilter {

    private final ProblemDetailWriter problemDetailWriter;
    private final long maxBodyBytes;

    public RequestBodyLimitFilter(
            ProblemDetailWriter problemDetailWriter,
            @Value("${webblock.http.max-body-bytes:1572864}") long maxBodyBytes) {
        this.problemDetailWriter = problemDetailWriter;
        this.maxBodyBytes = maxBodyBytes;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (request.getContentLengthLong() > maxBodyBytes) {
            problemDetailWriter.write(request, response, ErrorCode.PAYLOAD_TOO_LARGE);
            return;
        }
        try {
            chain.doFilter(new LimitedRequest(request, maxBodyBytes), response);
        } catch (BodyTooLargeException e) {
            if (response.isCommitted()) {
                throw e;
            }
            problemDetailWriter.write(request, response, ErrorCode.PAYLOAD_TOO_LARGE);
        }
    }

    private static final class LimitedRequest extends HttpServletRequestWrapper {

        private final long limit;
        private ServletInputStream stream;

        LimitedRequest(HttpServletRequest request, long limit) {
            super(request);
            this.limit = limit;
        }

        @Override
        public ServletInputStream getInputStream() throws IOException {
            if (stream == null) {
                stream = new CountingInputStream(super.getInputStream(), limit);
            }
            return stream;
        }

        @Override
        public BufferedReader getReader() throws IOException {
            String name = getCharacterEncoding();
            Charset charset = name == null ? StandardCharsets.ISO_8859_1 : Charset.forName(name);
            return new BufferedReader(new InputStreamReader(getInputStream(), charset));
        }
    }

    private static final class CountingInputStream extends ServletInputStream {

        private final ServletInputStream delegate;
        private final long limit;
        private long count;

        CountingInputStream(ServletInputStream delegate, long limit) {
            this.delegate = delegate;
            this.limit = limit;
        }

        private void add(long bytes) throws IOException {
            if (bytes > 0) {
                count += bytes;
                if (count > limit) {
                    throw new BodyTooLargeException(limit);
                }
            }
        }

        @Override
        public int read() throws IOException {
            int value = delegate.read();
            if (value >= 0) {
                add(1);
            }
            return value;
        }

        @Override
        public int read(byte[] buffer, int offset, int length) throws IOException {
            int read = delegate.read(buffer, offset, length);
            add(read);
            return read;
        }

        @Override
        public boolean isFinished() {
            return delegate.isFinished();
        }

        @Override
        public boolean isReady() {
            return delegate.isReady();
        }

        @Override
        public void setReadListener(ReadListener listener) {
            delegate.setReadListener(listener);
        }
    }
}
