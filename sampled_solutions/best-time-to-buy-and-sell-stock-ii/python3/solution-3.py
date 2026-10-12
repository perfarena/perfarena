def max_profit(prices):
    if len(prices) <= 1:
        return 0
    
    buy = prices[0]
    profit = 0
    
    for i in range(1, len(prices)):
        if prices[i] < prices[i - 1]:
            profit += prices[i - 1] - buy
            buy = prices[i]
    
    # Handle the case where the last price was a peak
    profit += prices[-1] - buy
    
    return profit

# Test the function
prices = [7, 1, 5, 3, 6, 4]
print(max_profit(prices))  # Output: 7