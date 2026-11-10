def maxProfit(prices):
    maxProfit = 0
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            maxProfit += prices[i] - prices[i - 1]
    return maxProfit

# Test the function
prices = [7, 1, 5, 3, 6, 4]
print(maxProfit(prices))  # Output: 7