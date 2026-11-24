func lastStoneWeight(_ stones: [Int]) -> Int {
    var heap = Heap<Int>() // Create a max heap
    
    // Insert all elements into the heap
    for stone in stones {
        heap.insert(stone)
    }
    
    // Perform smash operation until only one element is left
    while heap.count > 1 {
        let y = heap.remove()!
        let x = heap.remove()!
        
        if x != y {
            heap.insert(y - x)
        }
    }
    
    // Return the weight of the last remaining stone
    return heap.peek() ?? 0
}