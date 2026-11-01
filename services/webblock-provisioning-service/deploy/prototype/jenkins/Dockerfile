# The prototype's Jenkins controller image (decision 0014, owner answer 3), built locally by
# deploy/kind/proto-ci.sh as webblock-proto-jenkins:local. Both base images are passed by digest as
# build arguments from deploy/prototype/versions.sh. No Docker CLI or socket: images are built by the
# separate BuildKit daemon through buildctl.
ARG JENKINS_BASE
ARG BUILDKIT_IMAGE
FROM ${BUILDKIT_IMAGE} AS buildkit

FROM ${JENKINS_BASE}
COPY --from=buildkit /usr/bin/buildctl /usr/local/bin/buildctl
# Plain COPY keeps the mode proto-ci.sh sets (0755); --chmod would need BuildKit in the host's Docker.
COPY webblock-buildctl /usr/local/bin/webblock-buildctl
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --latest false --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY casc.yaml /var/jenkins_conf/casc.yaml
COPY config.xml /usr/share/jenkins/ref/jobs/webblock-site-build/config.xml
ENV CASC_JENKINS_CONFIG=/var/jenkins_conf/casc.yaml \
    JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xmx512m -Xms128m"
