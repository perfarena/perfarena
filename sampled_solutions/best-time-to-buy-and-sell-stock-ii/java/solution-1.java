int maxProfit = 0;
int i = 0;
while (i < prices.length - 1) {
    // Find valley
    while (i < prices.length - 1 && prices[i] >= prices[i + 1]) {
        i++;
    }
    int valley = prices[i];
    
    // Find peak
    while (i < prices.length - 1 && prices[i] <= prices[i + 1]) {
        i++;
    }
    int peak = prices[i];
    
    maxProfit += peak - valley;
}
return maxProfit;