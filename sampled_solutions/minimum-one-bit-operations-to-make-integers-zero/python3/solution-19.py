def find_highest_altitude(gain):
    altitudes = [0]  # Initialize the list with starting altitude 0
    for i in range(len(gain)):
        altitudes.append(altitudes[i] + gain[i])  # Calculate altitude at each point

    return max(altitudes)  # Return the maximum altitude reached

# Example usage
gain = [-5, 1, 5, 0, -7]
highest_altitude = find_highest_altitude(gain)
print(highest_altitude)