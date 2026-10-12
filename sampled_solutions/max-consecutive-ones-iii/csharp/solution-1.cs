// Create a priority queue
PriorityQueue<int> pq = new PriorityQueue<int>();

// Add stones to the priority queue
foreach (int stone in stones) {
    pq.Enqueue(-stone); // Using negative values for max heap simulation
}

// Process stones in the priority queue
while (pq.Count >= 2) {
    int weight1 = -pq.Dequeue();
    int weight2 = -pq.Dequeue();
    
    if (weight1 != weight2) {
        int newWeight = weight1 - weight2;
        pq.Enqueue(-newWeight);
    }
}

// Return the remaining stone weight or zero
return pq.Count == 0 ? 0 : -pq.Peek();