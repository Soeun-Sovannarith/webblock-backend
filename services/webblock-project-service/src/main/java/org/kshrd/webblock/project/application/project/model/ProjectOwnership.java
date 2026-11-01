package org.kshrd.webblock.project.application.project.model;

import java.util.UUID;

/** Who owns a live project and whether it is suspended: what an access decision needs, read without the hostname. */
public record ProjectOwnership(UUID ownerUserId, boolean suspended) {}
