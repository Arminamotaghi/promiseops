def train_candidate(connection, *, train_end, validation_end, as_of):
    """At least two approaches; mature labels at the correct cutoffs; MLflow lineage."""
    raise NotImplementedError("Train and evaluate a reproducible candidate")


def compare_candidate(candidate, champion, cohort):
    """Both models must use exactly the same validation orders and public gates."""
    raise NotImplementedError("Implement candidate gates and record the decision")
