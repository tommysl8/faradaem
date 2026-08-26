"""What a circuit can refuse, and why.

These live apart from everything else because everything else raises them.
A module that describes a topology and a module that measures one both need
to say "that combination cannot be run", and neither should have to import
the other to do it.
"""


class UnknownCircuitError(KeyError):
    """Raised when a circuit id is not in the catalogue."""


class CircuitInputError(ValueError):
    """A parameter combination the circuit cannot be run at. Maps to HTTP 400.

    Every value was individually inside its declared range, but together they
    ask for something the simulator cannot deliver. That is a bad request, not
    a server fault, and the message has to say which way to move.
    """


class BiasError(CircuitInputError):
    """Raised when a bias leaves nothing measurable.

    The run itself succeeded; there was simply nothing in it to measure.
    """


class NoStepResponseError(CircuitInputError):
    """Raised when a circuit has no step testbench to run."""


class NoDatasheetError(CircuitInputError):
    """Raised when a circuit has no rejection testbench to run."""


class NoFloorplanError(CircuitInputError):
    """Raised when a circuit has no floorplan to compute."""
