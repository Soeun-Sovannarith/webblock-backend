package org.kshrd.webblock.project.infrastructure.config.catalog;

import java.net.URI;
import java.net.URISyntaxException;
import org.springframework.boot.context.properties.ConfigurationProperties;

/** webblock.catalog: the catalog database connections and the website storage defaults. */
@ConfigurationProperties("webblock.catalog")
public record CatalogProperties(
        Datasource datasource, Migrations migrations, long storageLimitBytes, long maxImageBytes) {

    /** The runtime login, a member of catalog_platform. */
    public record Datasource(String url, String username, String password, int maximumPoolSize) {

        /** Without the password or the URL's query (which can carry password=), so logging never writes either. */
        @Override
        public String toString() {
            return "Datasource[url=" + withoutQuery(url) + ", username=" + username + ", password=***, maximumPoolSize="
                    + maximumPoolSize + "]";
        }

        /** Scheme, host, port and database only; any URL that does not read that way is masked whole. */
        private static String withoutQuery(String url) {
            if (url == null || !url.startsWith("jdbc:")) {
                return "***";
            }
            try {
                URI uri = new URI(url.substring("jdbc:".length()));
                if (uri.getHost() == null || uri.getRawUserInfo() != null) {
                    return "***";
                }
                return "jdbc:" + uri.getScheme() + "://" + uri.getHost()
                        + (uri.getPort() < 0 ? "" : ":" + uri.getPort()) + uri.getRawPath();
            } catch (URISyntaxException e) {
                return "***";
            }
        }
    }

    /** The migration login, which may SET ROLE catalog_owner; the URL is the datasource's. */
    public record Migrations(boolean enabled, String user, String password) {

        /** Without the password, so that logging the settings never writes it. */
        @Override
        public String toString() {
            return "Migrations[enabled=" + enabled + ", user=" + user + ", password=***]";
        }
    }
}
