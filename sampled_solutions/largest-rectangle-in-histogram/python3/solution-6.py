def largestRectangleArea(heights):
    if len(heights) == 0:
        return 0
    if len(heights) == 1:
        return heights[0]

    maxArea = 0
    stack = [0]

    for i in range(1, len(heights)):
        while stack and heights[i] < heights[stack[-1]]:
            h = heights[stack.pop()]
            w = i if not stack else i - stack[-1] - 1
            maxArea = max(maxArea, h * w)
        stack.append(i)

    i = len(heights)
    while stack:
        h = heights[stack.pop()]
        w = i if not stack else i - stack[-1] - 1
        maxArea = max(maxArea, h * w)

    return maxArea

# Test the function with a sample histogram
histogram = [2, 1, 5, 6, 2, 3]
print(largestRectangleArea(histogram))  # Output should be 10