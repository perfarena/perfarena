def max_area(height):
    max_area = 0
    left, right = 0, len(height) - 1
    
    while left < right:
        h = min(height[left], height[right])
        max_area = max(max_area, h * (right - left))
        
        if height[left] < height[right]:
            left += 1
        else:
            right -= 1
    
    return max_area

# Example usage
height = [1, 8, 6, 2, 5, 4, 8, 3, 7]
print(max_area(height))  # Output: 49