package org.kshrd.webblock.project.application.template.handler;

import java.io.IOException;
import java.io.InputStream;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;
import org.kshrd.webblock.project.application.page.handler.PageRules;
import org.kshrd.webblock.project.application.page.handler.PuckValidator;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.template.model.SiteTemplate;
import org.kshrd.webblock.project.application.template.model.TemplatePage;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.ClassPathResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

/**
 * The site templates bundled with the service (no template store yet), checked once at start-up against everything
 * the wizard's insert would check later: an invalid file stops the service instead of failing a request after Payment
 * has admitted the website.
 */
@Component
public class TemplateCatalog {

    public static final String LOCATION = "site-templates/templates.json";
    private static final Pattern CODE = Pattern.compile("^[a-z][a-z0-9_]{0,99}$");
    private static final Pattern STYLE_KEY = Pattern.compile("^[a-z][a-z0-9-]{0,49}$");
    private static final int MAX_TEXT = 255;

    record TemplateFile(List<SiteTemplate> templates) {}

    private final List<SiteTemplate> templates;

    @Autowired
    public TemplateCatalog(JsonMapper jsonMapper, PuckValidator validator, PageRules rules) throws IOException {
        this(jsonMapper, validator, rules, new ClassPathResource(LOCATION));
    }

    TemplateCatalog(JsonMapper jsonMapper, PuckValidator validator, PageRules rules, Resource resource)
            throws IOException {
        TemplateFile file;
        try (InputStream in = resource.getInputStream()) {
            file = jsonMapper.readValue(in, TemplateFile.class);
        }
        if (file.templates() == null || file.templates().isEmpty()) {
            throw new IllegalStateException("No site template in " + resource.getDescription());
        }
        Set<String> codes = new HashSet<>();
        for (SiteTemplate template : file.templates()) {
            check(template, validator, rules, codes);
        }
        this.templates = List.copyOf(file.templates());
    }

    private static void check(SiteTemplate t, PuckValidator validator, PageRules rules, Set<String> codes) {
        String where = "Site template " + t.templateCode();
        if (t.templateCode() == null || !CODE.matcher(t.templateCode()).matches() || !codes.add(t.templateCode())) {
            throw new IllegalStateException(where + ": code missing, invalid or repeated");
        }
        checkDisplay(t, where);
        if (t.businessTypeCodes() == null || t.businessTypeCodes().isEmpty()) {
            throw new IllegalStateException(where + ": no business type");
        }
        if (!validator.knows(t.componentCatalogVersion())) {
            throw new IllegalStateException(where + ": unknown component catalog " + t.componentCatalogVersion());
        }
        if (t.pages() == null || t.pages().size() != 1) {
            throw new IllegalStateException(where + ": needs exactly 1 page (a website has one page)");
        }
        TemplatePage page = t.pages().getFirst();
        try {
            rules.checkedPage(
                    t.componentCatalogVersion(),
                    new NewPage(
                            page.name(),
                            page.path(),
                            page.puckTree(),
                            page.seoTitle(),
                            page.seoDescription(),
                            page.sortOrder()));
        } catch (WebBlockException e) {
            throw new IllegalStateException(where + ": page 0 breaks the page rules: " + describe(e));
        }
        if (!"/".equals(page.path())) {
            throw new IllegalStateException(where + ": has no home page \"/\"");
        }
    }

    /** The optional display fields: bounded text, a site-relative thumbnail path and a style key. */
    private static void checkDisplay(SiteTemplate t, String where) {
        for (String text : new String[] {t.name(), t.description(), t.badge(), t.thumbnailUrl()}) {
            if (text != null && text.length() > MAX_TEXT) {
                throw new IllegalStateException(where + ": a display field is longer than " + MAX_TEXT);
            }
        }
        if (t.thumbnailUrl() != null && !t.thumbnailUrl().startsWith("/")) {
            throw new IllegalStateException(where + ": thumbnailUrl must be a path starting with /");
        }
        if (t.styleKey() != null && !STYLE_KEY.matcher(t.styleKey()).matches()) {
            throw new IllegalStateException(where + ": styleKey is invalid");
        }
    }

    /** The refusal's code and its first field problem, as the API would answer them. */
    private static String describe(WebBlockException e) {
        return e.details().isEmpty()
                ? e.errorCode().code()
                : e.errorCode().code() + " at " + e.details().getFirst().field() + " "
                        + e.details().getFirst().code();
    }

    public List<SiteTemplate> all() {
        return templates;
    }

    public List<SiteTemplate> forBusinessType(String businessTypeCode) {
        return templates.stream().filter(t -> t.appliesTo(businessTypeCode)).toList();
    }

    /** The thumbnail of the template whose style key is given; empty for an unknown key or a template without one. */
    public Optional<String> thumbnailForStyleKey(String styleKey) {
        return templates.stream()
                .filter(t -> t.styleKey() != null && t.styleKey().equals(styleKey))
                .map(SiteTemplate::thumbnailUrl)
                .filter(Objects::nonNull)
                .findFirst();
    }

    /** The template with this code, whatever business type it applies to. */
    public Optional<SiteTemplate> find(String templateCode) {
        return templates.stream()
                .filter(t -> t.templateCode().equals(templateCode))
                .findFirst();
    }

    public Optional<SiteTemplate> find(String templateCode, String businessTypeCode) {
        return templates.stream()
                .filter(t -> t.templateCode().equals(templateCode) && t.appliesTo(businessTypeCode))
                .findFirst();
    }
}
