package org.kshrd.webblock.project.application.payment.job;

import java.util.UUID;

/** The durable job that releases an admission whose project was never created (decision P9). */
public record AdmissionReleaseJob(UUID projectId) {}
