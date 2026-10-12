def trap_water(heights):
    left, right = 0, len(heights) - 1
    leftMax = rightMax = accumulator = 0

    while left < right:
        if heights[right] >= heights[leftMax]:
            rightMax = max(rightMax, heights[right])
        else:
            accumulator += rightMax - heights[right]
        left += 1

        if heights[left] >= heights[rightMax]:
            leftMax = max(leftMax, heights[left])
        else:
            accumulator += leftMax - heights[left]
        right -= 1

    return accumulator