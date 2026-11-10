def find_largest_rectangle(heights):
    max_area = 0
    for i in range(len(heights)):
        left = i
        right = i
        while left > 0 and heights[left - 1] >= heights[i]:
            left -= 1
        while right < len(heights) - 1 and heights[right + 1] >= heights[i]:
            right += 1
        width = right - left + 1
        area = width * heights[i]
        max_area = max(max_area, area)
    return max_area

# Example usage
heights = [2, 1, 5, 6, 2, 3]
largest_area = find_largest_rectangle(heights)
print("The largest rectangle area is:", largest_area)