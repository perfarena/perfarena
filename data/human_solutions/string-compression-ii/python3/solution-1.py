def find_max_width(points):
    temp = [point[0] for point in points]  # Extract X coordinates
    temp.sort()  # Sort the X coordinates

    result = 0
    for i in range(1, len(temp)):
        result = max(result, temp[i] - temp[i - 1])  # Calculate differences and update result

    return result

# Example usage
points = [[8, 9], [7, 9], [7, 4], [9, 7]]
max_width = find_max_width(points)
print(max_width)