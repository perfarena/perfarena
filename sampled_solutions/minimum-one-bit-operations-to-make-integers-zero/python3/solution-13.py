def find_highest_altitude(gain):
    result = [0]  # Initialize the result array with altitude 0 at index 0
    max_altitude = 0  # Initialize the maximum altitude reached

    for i in range(len(gain)):
        result.append(result[i] + gain[i])  # Calculate the altitude at each point
        max_altitude = max(max_altitude, result[-1])  # Update the maximum altitude

    return max_altitude

# Test the function with sample data
gain = [-5, 1, 5, 0, -7]
print(find_highest_altitude(gain))  # Output should be 1