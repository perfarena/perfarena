def max_width(points):
    points.sort(key=lambda x: x[0])  # Sort points based on x-coordinate
    max_width = 0
    
    for i in range(1, len(points)):
        current_width = points[i][0] - points[i-1][0]
        max_width = max(max_width, current_width)
    
    return max_width