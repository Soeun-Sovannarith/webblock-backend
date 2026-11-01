package org.kshrd.webblock.project.application.wizard.model;

import org.kshrd.webblock.project.application.project.model.ProjectRecord;

/** The wizard's result: created is false for a repeat of an earlier, identical request. */
public record CreationOutcome(ProjectRecord project, boolean created) {}
