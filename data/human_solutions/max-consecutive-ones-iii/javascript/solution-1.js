// Initialize Max Priority Queue
const stonesHeap = new MaxPriorityQueue();

// Enqueue stone weights into the Heap
stones.forEach(weight => stonesHeap.enqueue(weight));

// Process stones until at most one stone is left
while (stonesHeap.size() > 1) {
    // Extract the two heaviest stones
    const stone1 = stonesHeap.dequeue().element;
    const stone2 = stonesHeap.dequeue().element;
    
    // Calculate the difference and add it back to the Heap
    const difference = Math.abs(stone1 - stone2);
    if (difference > 0) {
        stonesHeap.enqueue(difference);
    }
}

// Return the weight of the last remaining stone or 0 if no stones left
return stonesHeap.size() === 1 ? stonesHeap.front().element : 0;