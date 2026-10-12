def maxProfit(prices):
    if len(prices) == 1:
        return 0
    
    maxProfit = 0
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            maxProfit += prices[i] - prices[i - 1]
    
    return maxProfit