package org.kshrd.webblock.project.support;

import java.util.Set;
import java.util.TreeSet;
import org.springframework.context.ApplicationContext;
import org.springframework.web.servlet.handler.AbstractHandlerMethodMapping;
import org.springframework.web.servlet.handler.AbstractUrlHandlerMapping;
import org.springframework.web.servlet.mvc.method.RequestMappingInfo;

/** Every path pattern the context's handler mappings serve: controller mappings and URL (resource) mappings. */
public final class ServedPaths {

    private ServedPaths() {}

    public static Set<String> of(ApplicationContext context) {
        Set<String> paths = new TreeSet<>();
        for (AbstractHandlerMethodMapping<?> mapping :
                context.getBeansOfType(AbstractHandlerMethodMapping.class).values()) {
            for (Object info : mapping.getHandlerMethods().keySet()) {
                if (info instanceof RequestMappingInfo requestMapping) {
                    paths.addAll(requestMapping.getPatternValues());
                }
            }
        }
        for (AbstractUrlHandlerMapping mapping :
                context.getBeansOfType(AbstractUrlHandlerMapping.class).values()) {
            paths.addAll(mapping.getHandlerMap().keySet());
        }
        return paths;
    }
}
