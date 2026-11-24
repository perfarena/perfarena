def find_highest_altitude(gain):
    max_altitude = 0
    current_altitude = 0
    
    for change in gain:
        current_altitude += change
        max_altitude = max(max_altitude, current_altitude)
    
    return max_altitude

# Example usage
elevation_changes = [-5, 1, 5, -2, 3]
highest_altitude = find_highest_altitude(elevation_changes)
print(highest_altitude)