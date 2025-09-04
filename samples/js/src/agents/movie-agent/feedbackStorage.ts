import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export interface FeedbackRecord {
  id?: number;
  feedbackAuthId: string;
  agentSkillId: string;
  taskId: string;
  contextId: string;
  rating: number;
  domain: string;
  notes: string;
  proofOfPayment?: string;
  createdAt: string;
}

class FeedbackStorage {
  private dataPath: string;
  private data: FeedbackRecord[] = [];
  private nextId: number = 1;

  constructor() {
    this.dataPath = path.join(__dirname, 'feedback.json');
    this.loadData();
  }

  private loadData(): void {
    try {
      if (fs.existsSync(this.dataPath)) {
        const rawData = fs.readFileSync(this.dataPath, 'utf-8');
        this.data = JSON.parse(rawData);
        // Find the highest ID to set nextId
        this.nextId = Math.max(0, ...this.data.map(r => r.id || 0)) + 1;
      }
    } catch (error) {
      console.warn('Could not load feedback data, starting fresh:', error);
      this.data = [];
      this.nextId = 1;
    }
  }

  private saveData(): void {
    try {
      fs.writeFileSync(this.dataPath, JSON.stringify(this.data, null, 2));
    } catch (error) {
      console.error('Could not save feedback data:', error);
    }
  }

  addFeedback(record: Omit<FeedbackRecord, 'id' | 'createdAt'>): number {
    const newRecord: FeedbackRecord = {
      ...record,
      id: this.nextId++,
      createdAt: new Date().toISOString()
    };
    
    this.data.unshift(newRecord); // Add to beginning for newest first
    this.saveData();
    
    return newRecord.id!;
  }

  getFeedbackById(id: number): FeedbackRecord | null {
    return this.data.find(record => record.id === id) || null;
  }

  getFeedbackByDomain(domain: string): FeedbackRecord[] {
    return this.data.filter(record => record.domain === domain);
  }

  getFeedbackByRating(minRating?: number, maxRating?: number): FeedbackRecord[] {
    return this.data.filter(record => {
      if (minRating !== undefined && record.rating < minRating) return false;
      if (maxRating !== undefined && record.rating > maxRating) return false;
      return true;
    });
  }

  getAllFeedback(): FeedbackRecord[] {
    return [...this.data]; // Return a copy
  }

  getFeedbackStats(): {
    total: number;
    averageRating: number;
    byDomain: Record<string, number>;
    byRating: Record<number, number>;
  } {
    const total = this.data.length;
    const averageRating = total > 0 ? this.data.reduce((sum, record) => sum + record.rating, 0) / total : 0;
    
    const byDomain: Record<string, number> = {};
    const byRating: Record<number, number> = {};
    
    this.data.forEach(record => {
      byDomain[record.domain] = (byDomain[record.domain] || 0) + 1;
      byRating[record.rating] = (byRating[record.rating] || 0) + 1;
    });
    
    return {
      total,
      averageRating,
      byDomain,
      byRating
    };
  }

  close(): void {
    // No-op for JSON storage
  }
}

// Singleton instance
let feedbackStorage: FeedbackStorage | null = null;

export function getFeedbackDatabase(): FeedbackStorage {
  if (!feedbackStorage) {
    feedbackStorage = new FeedbackStorage();
  }
  return feedbackStorage;
}

export function closeFeedbackDatabase(): void {
  if (feedbackStorage) {
    feedbackStorage.close();
    feedbackStorage = null;
  }
}
