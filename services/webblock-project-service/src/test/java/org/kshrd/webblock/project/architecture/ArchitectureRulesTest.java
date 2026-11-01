package org.kshrd.webblock.project.architecture;

import static com.tngtech.archunit.base.DescribedPredicate.not;
import static com.tngtech.archunit.core.domain.JavaClass.Predicates.assignableTo;
import static com.tngtech.archunit.core.domain.JavaClass.Predicates.equivalentTo;
import static com.tngtech.archunit.core.domain.JavaClass.Predicates.resideInAPackage;
import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.classes;
import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;
import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noMethods;
import static com.tngtech.archunit.library.GeneralCodingRules.NO_CLASSES_SHOULD_ACCESS_STANDARD_STREAMS;
import static com.tngtech.archunit.library.GeneralCodingRules.NO_CLASSES_SHOULD_USE_FIELD_INJECTION;
import static com.tngtech.archunit.library.GeneralCodingRules.NO_CLASSES_SHOULD_USE_JAVA_UTIL_LOGGING;

import com.tngtech.archunit.core.domain.JavaClass;
import com.tngtech.archunit.core.importer.ImportOption;
import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchCondition;
import com.tngtech.archunit.lang.ArchRule;
import com.tngtech.archunit.lang.ConditionEvents;
import com.tngtech.archunit.lang.SimpleConditionEvent;
import java.lang.reflect.Method;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import org.kshrd.webblock.project.WebBlockApplication;
import org.kshrd.webblock.project.common.infrastructure.config.StandIn;
import org.kshrd.webblock.project.common.infrastructure.security.JwtDecoderConfig;
import org.kshrd.webblock.project.common.presentation.envelope.ApiResponse;
import org.kshrd.webblock.project.common.presentation.envelope.ResponseEnvelopeAdvice;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.repository.Repository;
import org.springframework.web.bind.annotation.RestController;

@AnalyzeClasses(packages = "org.kshrd.webblock.project", importOptions = ImportOption.DoNotIncludeTests.class)
public class ArchitectureRulesTest {

    @ArchTest
    static final ArchRule controllersDoNotUseRepositories = noClasses()
            .that()
            .areAnnotatedWith(RestController.class)
            .should()
            .dependOnClassesThat(assignableTo(Repository.class));

    /**
     * A feature's Spring configuration is wiring, not API: other code reaches the feature through its application
     * classes. Only common's configuration is public, for the web slice tests that import it.
     */
    @ArchTest
    static final ArchRule featureConfigurationIsPackagePrivate = classes()
            .that()
            .areAnnotatedWith(Configuration.class)
            .and()
            .resideOutsideOfPackage("org.kshrd.webblock.project.common..")
            .should()
            .notBePublic();

    @ArchTest
    static final ArchRule commonDependsOnNoFeature = noClasses()
            .that()
            .resideInAPackage("org.kshrd.webblock.project.common..")
            .should()
            .dependOnClassesThat(resideInAPackage("org.kshrd.webblock.project..")
                    .and(not(resideInAPackage("org.kshrd.webblock.project.common..")))
                    .and(not(equivalentTo(WebBlockApplication.class))));

    /**
     * Spec section 6.4: ApiResponse has no error form. Only the success advice uses it, and the advice never wraps a
     * status of 400 or more, so no error path can answer with the envelope.
     */
    @ArchTest
    public static final ArchRule onlyTheEnvelopeAdviceUsesApiResponse = noClasses()
            .that()
            .doNotBelongToAnyOf(ApiResponse.class, ResponseEnvelopeAdvice.class)
            .should()
            .dependOnClassesThat(equivalentTo(ApiResponse.class));

    /**
     * Spec section 3: only the integration features' infrastructure (payment, generation, publication) calls out over
     * HTTP. Classes are named so no client is required.
     */
    @ArchTest
    static final ArchRule onlyIntegrationModulesCallExternalHttpClients = noClasses()
            .that()
            .resideOutsideOfPackages(
                    "org.kshrd.webblock.project.infrastructure.*.publication..",
                    "org.kshrd.webblock.project.infrastructure.*.payment..",
                    "org.kshrd.webblock.project.infrastructure.*.generation..")
            // The JWT decoder's discovery/JWKS fetch needs a RestOperations with timeouts (NimbusJwtDecoder's API).
            .and()
            .doNotHaveFullyQualifiedName(JwtDecoderConfig.class.getName())
            .should()
            .dependOnClassesThat()
            .haveFullyQualifiedName("org.springframework.web.client.RestClient")
            .orShould()
            .dependOnClassesThat()
            .haveFullyQualifiedName("org.springframework.web.client.RestTemplate")
            .orShould()
            .dependOnClassesThat()
            .haveFullyQualifiedName("org.springframework.web.reactive.function.client.WebClient")
            .orShould()
            .dependOnClassesThat()
            .haveFullyQualifiedName("java.net.http.HttpClient")
            .allowEmptyShould(true);

    /**
     * Modulith's event publication registry is excluded (spike S1), so an {@code @ApplicationModuleListener} would run
     * without being recorded: its work is lost on a crash. Durable work goes through the Namastack outbox instead.
     */
    @ArchTest
    static final ArchRule noApplicationModuleListeners = noMethods()
            .should()
            .beAnnotatedWith("org.springframework.modulith.events.ApplicationModuleListener")
            .because("the event publication registry is excluded; durable work goes through the outbox")
            .allowEmptyShould(true);

    // Layers (TR-5, decision 0016): the layer-first layout of Notion 6.1. The four layers are the top packages
    // (presentation, application, domain, infrastructure), each split by type folder and feature; common keeps its own
    // four layers. Features are not Modulith modules, so these rules state only the direction of the layers (Notion 6.1
    // arrows: presentation to application, application to infrastructure and domain, infrastructure to domain).
    // Infrastructure also uses application: a stand-in or HTTP client implements an application port, a job handler
    // calls a use case.

    private static final String BASE_PACKAGE = "org.kshrd.webblock.project";

    private static final String[] PRESENTATION = {
        BASE_PACKAGE + ".presentation..", BASE_PACKAGE + ".common.presentation.."
    };
    private static final String[] APPLICATION = {BASE_PACKAGE + ".application..", BASE_PACKAGE + ".common.application.."
    };
    private static final String[] DOMAIN = {BASE_PACKAGE + ".domain..", BASE_PACKAGE + ".common.domain.."};
    private static final String[] INFRASTRUCTURE = {
        BASE_PACKAGE + ".infrastructure..", BASE_PACKAGE + ".common.infrastructure.."
    };

    private static String[] layers(String[]... groups) {
        return Stream.of(groups).flatMap(Stream::of).toArray(String[]::new);
    }

    /** Every class below the base package sits in one of the four layers (its own or common's). */
    @ArchTest
    static final ArchRule featureClassesSitInALayer = classes()
            .that()
            .resideInAPackage(BASE_PACKAGE + ".*..")
            .and()
            .doNotHaveSimpleName("package-info")
            .should()
            .resideInAnyPackage(layers(PRESENTATION, APPLICATION, DOMAIN, INFRASTRUCTURE));

    /** Presentation uses application and common; never infrastructure (and never a feature's domain, below). */
    @ArchTest
    static final ArchRule presentationDoesNotUseInfrastructure = noClasses()
            .that()
            .resideInAnyPackage(PRESENTATION)
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage(INFRASTRUCTURE)
            .allowEmptyShould(true);

    /**
     * TR-4, Notion 6.1 arrows: presentation calls application, never a feature's domain types. Common's presentation
     * keeps using common's domain (the error catalogue, request IDs); feature presentation may use common's too.
     */
    @ArchTest
    public static final ArchRule presentationDoesNotUseDomain = noClasses()
            .that()
            .resideInAPackage(BASE_PACKAGE + ".presentation..")
            .should()
            .dependOnClassesThat()
            .resideInAPackage(BASE_PACKAGE + ".domain..")
            .allowEmptyShould(true);

    /** Application uses domain, common and infrastructure (repositories, config beans); never presentation. */
    @ArchTest
    static final ArchRule applicationDoesNotUsePresentation = noClasses()
            .that()
            .resideInAnyPackage(APPLICATION)
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage(PRESENTATION)
            .allowEmptyShould(true);

    /** Domain uses only domain classes (its own and common's), the JDK and JPA/Hibernate mapping annotations. */
    @ArchTest
    static final ArchRule domainUsesOnlyDomain = noClasses()
            .that()
            .resideInAnyPackage(DOMAIN)
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage(layers(PRESENTATION, APPLICATION, INFRASTRUCTURE))
            .allowEmptyShould(true);

    /**
     * Allow-list: domain classes use only the JDK, JPA and Hibernate mapping types, Spring Data's {@code domain} types
     * ({@code Persistable}), the UUIDv7 generator and domain classes ({@code domain} or {@code common.domain}). No
     * Spring stereotype, JDBC, Jackson, web, HTTP or servlet type.
     */
    @ArchTest
    static final ArchRule domainUsesOnlyAllowedLibraries = classes()
            .that()
            .resideInAnyPackage(DOMAIN)
            .should()
            .onlyDependOnClassesThat()
            .resideInAnyPackage(
                    "java..",
                    "jakarta.persistence..",
                    "org.hibernate.annotations..",
                    "org.hibernate.type..",
                    "org.hibernate.dialect.type..",
                    "org.springframework.data.domain..",
                    "com.fasterxml.uuid..",
                    "org.kshrd.webblock.project..domain..")
            .allowEmptyShould(true);

    /** Infrastructure uses application (ports it implements) and domain; it never reaches into presentation. */
    @ArchTest
    static final ArchRule infrastructureDoesNotUsePresentation = noClasses()
            .that()
            .resideInAnyPackage(INFRASTRUCTURE)
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage(PRESENTATION)
            .allowEmptyShould(true);

    // Type folders (TR-5, decision 0016). Inside a layer every class sits in a type folder and a feature folder, in the
    // order Notion 6.1 shows: presentation/<type>/<feature>, application/<feature>/<type>, domain/<feature>/<type>,
    // infrastructure/<type>/<feature>. common keeps common/<layer>/<type>.

    /** The layers whose type folder comes before the feature folder (presentation/rest/project). */
    private static final Set<String> TYPE_BEFORE_FEATURE = Set.of("presentation", "infrastructure");

    /**
     * TR-5, decision 0016: request, response and validation types sit in application, as Notion 6.1 shows; use cases
     * sit in application/handler. TR-4, decision 0011: gRPC services sit in presentation/grpc.
     */
    private static final Map<String, Set<String>> TYPE_FOLDERS = Map.of(
            "presentation", Set.of("rest", "grpc", "messaging"),
            "application", Set.of("handler", "model", "port", "job", "config", "request", "response", "validation"),
            "domain", Set.of("model"),
            "infrastructure", Set.of("config", "persistence", "client", "messaging", "standin"));

    /**
     * The folders of common's own layout: the envelope, error handling, request filters, the calling user, the gRPC
     * server, the error catalogue and the security plumbing, next to the folders it shares with features.
     */
    private static final Map<String, Set<String>> COMMON_TYPE_FOLDERS = Map.of(
            "presentation", Set.of("controller", "grpc", "envelope", "error", "filter", "security"),
            "application", Set.of("service", "model", "port", "job", "config", "request", "response", "validation"),
            "domain", Set.of("entity", "model", "error", "support"),
            "infrastructure", Set.of("config", "persistence", "client", "messaging", "standin", "security"));

    /** Every top-level class of a layer sits directly in one type folder and one feature folder of its layer. */
    @ArchTest
    static final ArchRule layerClassesSitInATypeFolder = classes()
            .that()
            .resideInAnyPackage(layers(PRESENTATION, APPLICATION, DOMAIN, INFRASTRUCTURE))
            .and()
            .areTopLevelClasses()
            .and()
            .doNotHaveSimpleName("package-info")
            .should(new ArchCondition<JavaClass>("sit in an allowed type folder of their layer") {
                @Override
                public void check(JavaClass item, ConditionEvents events) {
                    String[] path = item.getPackageName()
                            .substring(BASE_PACKAGE.length() + 1)
                            .split("\\.", -1);
                    boolean common = path[0].equals("common");
                    String layer = common ? path[1] : path[0];
                    Set<String> allowed =
                            new TreeSet<>((common ? COMMON_TYPE_FOLDERS : TYPE_FOLDERS).getOrDefault(layer, Set.of()));
                    int type = !common && TYPE_BEFORE_FEATURE.contains(layer) ? 1 : 2;
                    boolean ok = path.length == 3 && allowed.contains(path[type]);
                    events.add(new SimpleConditionEvent(
                            item,
                            ok,
                            item.getName() + " in package " + item.getPackageName() + (ok ? " sits" : " does not sit")
                                    + " in a type folder of " + layer + " " + allowed));
                }
            })
            .allowEmptyShould(true);

    // gRPC (TR-4, design G22, decision 0011): the stubs generated from contracts/proto (package webblock..) and the
    // grpc-java API stay at the edge, in presentation/grpc, as controllers keep Spring MVC there.

    private static final String[] PRESENTATION_GRPC = {
        BASE_PACKAGE + ".presentation.grpc..", BASE_PACKAGE + ".common.presentation.grpc.."
    };

    /** Every gRPC service, and so every class the server registers, sits in a presentation/grpc folder. */
    @ArchTest
    public static final ArchRule bindableServicesSitInPresentationGrpc = classes()
            .that()
            .implement(io.grpc.BindableService.class)
            .should()
            .resideInAnyPackage(PRESENTATION_GRPC)
            .allowEmptyShould(true);

    /** Only presentation/grpc classes use io.grpc or the generated contract types. */
    @ArchTest
    public static final ArchRule onlyPresentationGrpcUsesGrpcTypes = noClasses()
            .that()
            .resideOutsideOfPackages(PRESENTATION_GRPC)
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage("io.grpc..", "webblock..")
            .allowEmptyShould(true);

    @ArchTest
    static final ArchRule noStandardStreams = NO_CLASSES_SHOULD_ACCESS_STANDARD_STREAMS;

    @ArchTest
    static final ArchRule noJavaUtilLogging = NO_CLASSES_SHOULD_USE_JAVA_UTIL_LOGGING;

    @ArchTest
    static final ArchRule noFieldInjection = NO_CLASSES_SHOULD_USE_FIELD_INJECTION;

    /**
     * Decision 0002: method security is off (SecurityConfig has no
     * {@code @EnableMethodSecurity}), so {@code @PreAuthorize} and its kin would be silently dead, and a handler would
     * read as protected while every caller reaches it. Authorisation lives in the filter chains and in the services'
     * owner checks; /internal has no caller identity at all. So no class of the main code carries one, on the class or
     * on a method, directly or through a meta-annotation. Enabling method security is one deliberate decision that
     * changes this rule with it.
     */
    @ArchTest
    public static final ArchRule noMethodSecurityAnywhere = classes()
            .should(new ArchCondition<JavaClass>("carry no method security (@PreAuthorize and its kin)") {
                @Override
                public void check(JavaClass item, ConditionEvents events) {
                    Class<?> type = item.reflect();
                    if (MethodSecurity.carriesMethodSecurity(type)) {
                        events.add(SimpleConditionEvent.violated(item, item.getName() + " carries method security"));
                    }
                    for (Method method : type.getDeclaredMethods()) {
                        if (MethodSecurity.carriesMethodSecurity(method)) {
                            events.add(SimpleConditionEvent.violated(
                                    item, item.getName() + "." + method.getName() + " carries method security"));
                        }
                    }
                }
            })
            .allowEmptyShould(true);

    /** Every stand-in exists only under exactly the prototype profile (plan rule; production refuses stand-ins). */
    @ArchTest
    public static final ArchRule standInsAreOnlyInThePrototypeProfile = classes()
            .that()
            .implement(StandIn.class)
            .and()
            .areNotInterfaces()
            .should(
                    new com.tngtech.archunit.lang.ArchCondition<com.tngtech.archunit.core.domain.JavaClass>(
                            "be annotated @Profile(\"prototype\") and nothing else") {
                        @Override
                        public void check(
                                com.tngtech.archunit.core.domain.JavaClass item,
                                com.tngtech.archunit.lang.ConditionEvents events) {
                            boolean ok = item.tryGetAnnotationOfType(
                                            org.springframework.context.annotation.Profile.class)
                                    .map(profile ->
                                            java.util.Arrays.equals(profile.value(), new String[] {"prototype"}))
                                    .orElse(false);
                            events.add(new com.tngtech.archunit.lang.SimpleConditionEvent(
                                    item,
                                    ok,
                                    item.getName() + (ok ? " is" : " is not") + " @Profile(\"prototype\") only"));
                        }
                    })
            .allowEmptyShould(true);
}
