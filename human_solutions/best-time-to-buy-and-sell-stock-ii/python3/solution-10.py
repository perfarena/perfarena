def maxProfit(prices):
    if len(prices) <= 1:
        return 0
    
    cash = 0
    stock = -prices[0]
    
    for i in range(1, len(prices)):
        prev_cash = cash
        cash = max(cash, stock + prices[i])
        stock = max(stock, prev_cash - prices[i])
    
    return cash

# Test the function
prices = [7, 1, 5, 3, 6, 4]
print(maxProfit(prices))  # Output: 7