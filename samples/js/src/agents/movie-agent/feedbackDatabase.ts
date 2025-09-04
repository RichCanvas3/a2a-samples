import sqlite3 from 'sqlite3';
import { promisify } from 'util';
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

class FeedbackDatabase {
  private db: sqlite3.Database;
  private dbRun: (sql: string, params?: any[]) => Promise<sqlite3.RunResult>;
  private dbGet: (sql: string, params?: any[]) => Promise<any>;
  private dbAll: (sql: string, params?: any[]) => Promise<any[]>;

  constructor() {
    const dbPath = path.join(__dirname, 'feedback.db');
    this.db = new sqlite3.Database(dbPath);
    
    // Promisify database methods
    this.dbRun = promisify(this.db.run.bind(this.db));
    this.dbGet = promisify(this.db.get.bind(this.db));
    this.dbAll = promisify(this.db.all.bind(this.db));
    
    this.initializeDatabase();
  }

  private async initializeDatabase(): Promise<void> {
    const createTableSQL = `
      CREATE TABLE IF NOT EXISTS feedback_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feedbackAuthId TEXT NOT NULL,
        agentSkillId TEXT NOT NULL,
        taskId TEXT,
        contextId TEXT,
        rating INTEGER NOT NULL,
        domain TEXT NOT NULL,
        notes TEXT,
        proofOfPayment TEXT,
        createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
      )
    `;
    
    await this.dbRun(createTableSQL);
    console.info('Feedback database initialized');
  }

  async addFeedback(record: Omit<FeedbackRecord, 'id' | 'createdAt'>): Promise<number> {
    const insertSQL = `
      INSERT INTO feedback_records 
      (feedbackAuthId, agentSkillId, taskId, contextId, rating, domain, notes, proofOfPayment)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `;
    
    const result = await this.dbRun(insertSQL, [
      record.feedbackAuthId,
      record.agentSkillId,
      record.taskId,
      record.contextId,
      record.rating,
      record.domain,
      record.notes,
      record.proofOfPayment || null
    ]);
    
    return result.lastID;
  }

  async getFeedbackById(id: number): Promise<FeedbackRecord | null> {
    const selectSQL = 'SELECT * FROM feedback_records WHERE id = ?';
    const result = await this.dbGet(selectSQL, [id]);
    return result || null;
  }

  async getFeedbackByDomain(domain: string): Promise<FeedbackRecord[]> {
    const selectSQL = 'SELECT * FROM feedback_records WHERE domain = ? ORDER BY createdAt DESC';
    return await this.dbAll(selectSQL, [domain]);
  }

  async getFeedbackByRating(minRating?: number, maxRating?: number): Promise<FeedbackRecord[]> {
    let selectSQL = 'SELECT * FROM feedback_records';
    const params: any[] = [];
    
    if (minRating !== undefined || maxRating !== undefined) {
      const conditions: string[] = [];
      if (minRating !== undefined) {
        conditions.push('rating >= ?');
        params.push(minRating);
      }
      if (maxRating !== undefined) {
        conditions.push('rating <= ?');
        params.push(maxRating);
      }
      selectSQL += ' WHERE ' + conditions.join(' AND ');
    }
    
    selectSQL += ' ORDER BY createdAt DESC';
    return await this.dbAll(selectSQL, params);
  }

  async getAllFeedback(): Promise<FeedbackRecord[]> {
    const selectSQL = 'SELECT * FROM feedback_records ORDER BY createdAt DESC';
    return await this.dbAll(selectSQL);
  }

  async getFeedbackStats(): Promise<{
    total: number;
    averageRating: number;
    byDomain: Record<string, number>;
    byRating: Record<number, number>;
  }> {
    const totalResult = await this.dbGet('SELECT COUNT(*) as count FROM feedback_records');
    const avgResult = await this.dbGet('SELECT AVG(rating) as avg FROM feedback_records');
    const domainResult = await this.dbAll('SELECT domain, COUNT(*) as count FROM feedback_records GROUP BY domain');
    const ratingResult = await this.dbAll('SELECT rating, COUNT(*) as count FROM feedback_records GROUP BY rating ORDER BY rating');
    
    const byDomain: Record<string, number> = {};
    domainResult.forEach((row: any) => {
      byDomain[row.domain] = row.count;
    });
    
    const byRating: Record<number, number> = {};
    ratingResult.forEach((row: any) => {
      byRating[row.rating] = row.count;
    });
    
    return {
      total: totalResult.count,
      averageRating: avgResult.avg || 0,
      byDomain,
      byRating
    };
  }

  close(): void {
    this.db.close();
  }
}

// Singleton instance
let feedbackDb: FeedbackDatabase | null = null;

export function getFeedbackDatabase(): FeedbackDatabase {
  if (!feedbackDb) {
    feedbackDb = new FeedbackDatabase();
  }
  return feedbackDb;
}

export function closeFeedbackDatabase(): void {
  if (feedbackDb) {
    feedbackDb.close();
    feedbackDb = null;
  }
}
