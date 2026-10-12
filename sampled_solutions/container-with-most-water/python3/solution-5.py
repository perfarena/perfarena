def max_area(heights):
    max_area = 0
    left = 0
    right = len(heights) - 1

    while left < right:
        width = right - left
        current_height = min(heights[left], heights[right])
        current_area = width * current_height
        max_area = max(max_area, current_area)

        if heights[left] < heights[right]:
            left += 1
        else:
            right -= 1

    return max_area

# Example usage
heights = [1, 8, 6, 2, 5, 4, 8, 3, 7]
print(max_area(heights))  # Output: 49