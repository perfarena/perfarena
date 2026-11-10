def findMaxConsecutiveOnes(nums, k):
    i = 0
    zeroCount = 0
    result = 0

    for j in range(len(nums)):
        if nums[j] == 0:
            zeroCount += 1

        while zeroCount > k:
            if nums[i] == 0:
                zeroCount -= 1
            i += 1

        result = max(result, j - i + 1)

    return result

# Example usage
nums = [1, 1, 0, 1, 1, 0, 1, 1, 1]
k = 2
print(findMaxConsecutiveOnes(nums, k))  # Output: 6