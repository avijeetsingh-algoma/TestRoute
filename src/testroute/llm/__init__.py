"""LLM client package.

Component: C5 (LLM Client).

Responsibility:
    Holds the provider interface (``base``) and the providers behind it.
    Milestone 2 provides ``fake`` (deterministic canned responses for the test
    suite). ``ollama``, ``deepseek`` and ``replay`` arrive in milestone 4.

Receives / Produces / Talks to: see ``base``.
Spec sections: 5 (C5), 7.
Feedback IDs: FB12 (model ids and fingerprints recorded per call).
"""
