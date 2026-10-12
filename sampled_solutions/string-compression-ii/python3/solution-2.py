def maxWidthOfVerticalArea(points):
    points.sort(key=lambda x: x[0])  # Sort points by x-coordinate
    max_diff = 0
    
    for i in range(1, len(points)):
        max_diff = max(max_diff, points[i][0] - points[i-1][0])
    
    return max_diff

# Test the function with examples
points1 = [[8, 7], [9, 8], [7, 99]]
points2 = [[1, 3], [1, 4], [2, 5], [6, 8], [3, 6], [9, 9]]
print(maxWidthOfVerticalArea(points1))  # Output: 1
print(maxWidthOfVerticalArea(points2))  # Output: 3