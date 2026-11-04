"""The shared estate a client install lives in: what it accepts, and how it reads it.

``accepted_exposure`` is the closed accepted-exposure policy (plan section
4.12, M2-PR4): the exact tuples a client profile accepts, the privilege
ceiling per identity, one matcher per identity and the plan digest a
mutating run must acknowledge. ``genie_exposure`` and ``uc_exposure`` turn
what the audits read into those tuples; ``genie_single_space`` and
``sql_trust_scope`` are the two static controls an elevated App entry depends
on.
"""
