# Initialize profit variable
profit = 0

# Iterate through stock prices
for i in range(1, len(nums)):
    # Check if current price is higher than previous
    if nums[i] > nums[i - 1]:
        # Buy the stock and update profit
        profit += nums[i] - nums[i - 1]

# Return the total profit
return profit