def widest_vertical_area(points):
    points.sort()  # Sort the points based on x values
    max_width = 0
    
    for i in range(len(points) - 1):
        width = points[i + 1][0] - points[i][0]  # Calculate width between adjacent points
        max_width = max(max_width, width)  # Update maximum width
        
    return max_width

# Example usage
points = [(1, 1), (3, 2), (5, 3), (7, 4)]
result = widest_vertical_area(points)
print(result)  # Output: 2