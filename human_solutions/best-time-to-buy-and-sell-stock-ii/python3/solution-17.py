def max_profit(prices):
    if not prices:
        return 0

    max_profit = 0
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            max_profit += prices[i] - prices[i - 1]

    return max_profit

# Test the function with sample input
prices = [7, 1, 5, 3, 6, 4]
print(max_profit(prices))  # Output should be 7