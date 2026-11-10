def max_profit(prices):
    i = 0
    profit = 0

    while i < len(prices):
        # Find a valley
        while i < len(prices) - 1 and prices[i + 1] <= prices[i]:
            i += 1
        valley = prices[i]

        # Find a peak
        while i < len(prices) - 1 and prices[i + 1] > prices[i]:
            i += 1
        peak = prices[i]

        profit += peak - valley
        i += 1

    return profit

# Test the function
prices = [5, 7, 1, 5, 3, 6, 4]
print(max_profit(prices))  # Output: 7