def maxProduct(nums):
    prevMax = prevMin = result = nums[0]

    for i in range(1, len(nums)):
        current = nums[i]
        currentMax = max(current, current * prevMax, current * prevMin)
        currentMin = min(current, current * prevMax, current * prevMin)

        prevMax, prevMin = currentMax, currentMin
        result = max(result, currentMax)

    return result

# Test the function
nums = [2, 3, -2, 4]
print(maxProduct(nums))  # Output: 6