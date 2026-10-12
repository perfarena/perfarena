def maxProfit(nums):
    profit = 0
    for i in range(1, len(nums)):
        if nums[i] > nums[i - 1]:
            profit += nums[i] - nums[i - 1]
    return profit

# Test the function with the provided test case
nums = [7, 1, 5, 3, 6, 4]
print(maxProfit(nums))  # Output should be 7