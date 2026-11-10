def find_highest_altitude(gains):
    current = 0
    answer = 0
    
    for g in gains:
        current += g
        answer = max(answer, current)
    
    return answer

# Example usage
altitude_gains = [-5, -4, 1, 0, -6]
highest_altitude = find_highest_altitude(altitude_gains)
print(highest_altitude)