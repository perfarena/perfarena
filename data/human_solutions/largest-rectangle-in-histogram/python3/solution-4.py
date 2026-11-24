def largestRectangleArea(heights):
    stack = []
    result = 0
    heights.append(0)  # Append 0 to handle the case when all heights are increasing
    for i, h in enumerate(heights):
        while stack and h < heights[stack[-1]]:
            height = heights[stack.pop()]
            width = i if not stack else i - stack[-1] - 1
            result = max(result, height * width)
        stack.append(i)
    return result

# Test the function
histogram = [2, 1, 5, 6, 2, 3]
print(largestRectangleArea(histogram))  # Output: 10