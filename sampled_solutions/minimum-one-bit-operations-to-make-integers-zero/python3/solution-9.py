def find_highest_altitude(gain):
    high = 0
    for i in range(1, len(gain)):
        gain[i] += gain[i - 1]
        high = max(high, gain[i])
    return high

# Example usage
altitude_gain = [-5, 1, 5, 0, -7]
highest_altitude = find_highest_altitude(altitude_gain)
print(highest_altitude)