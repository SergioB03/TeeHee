# The autonomy's answer to one Observation. A plan for a tick that has passed must not be applied.
builtin_interfaces/Time stamp
bool ok
int32 tick                 # the tick of the observation this answers
float64 planning_micros    # time spent planning, in microseconds
Order[] orders
