"""Sample input for run.bat: a small function with several decision points.

run.bat uses this file when no file is given. It is our own example, not a
benchmark problem, so it is unlikely to be memorized by either model.
"""


def shipping_cost(weight_kg, express=False):
    """Return the shipping cost in dollars for a parcel.

    Parcels up to 1 kg cost 5. Up to 5 kg they cost 5 plus 2 per kg above 1.
    Heavier parcels cost 13 plus 1.5 per kg above 5. Express delivery doubles
    the price. A weight that is not positive raises ValueError.
    """
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    if weight_kg <= 1:
        cost = 5.0
    elif weight_kg <= 5:
        cost = 5.0 + 2.0 * (weight_kg - 1)
    else:
        cost = 13.0 + 1.5 * (weight_kg - 5)
    if express:
        cost *= 2
    return round(cost, 2)
