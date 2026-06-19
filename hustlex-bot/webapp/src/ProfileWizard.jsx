// webapp/src/ProfileWizard.jsx (optimized for fast loading)
import React, {useState, useEffect, useCallback} from 'react';

export default function ProfileWizard(){
  const [name, setName] = useState('');
  const [age, setAge] = useState('');
  const [sex, setSex] = useState('');
  const [cvFile, setCvFile] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    // Optimize Telegram WebApp initialization - run immediately without delay
    if (window.Telegram?.WebApp) {
      window.Telegram.WebApp.expand();
      // Pre-enable the main button for better UX
      if (window.Telegram.WebApp.MainButton) {
        window.Telegram.WebApp.MainButton.setText('Save Profile');
        window.Telegram.WebApp.MainButton.show();
      }
    }
  }, []);

  const submit = useCallback(async (e) => {
    e.preventDefault();
    if (isSubmitting) return;
    
    setIsSubmitting(true);
    const initData = window.Telegram?.WebApp?.initData || '';
    const fd = new FormData();
    fd.append('name', name);
    fd.append('age', age);
    fd.append('sex', sex);
    fd.append('initData', initData);
    if (cvFile) fd.append('cv', cvFile);

    try {
      const res = await fetch('/api/profile', { 
        method: 'POST', 
        body: fd,
        // Add timeout to prevent hanging
        signal: AbortSignal.timeout(30000)
      });
      if (res.ok) {
        // Store initData for authentication on job-listings page
        if (initData) {
          localStorage.setItem('telegram_init_data', initData);
        }
        // Redirect to job-listings as logged-in user
        window.location.href = 'https://hustlexet.vercel.app/job-listings';
      } else {
        alert('Save failed');
      }
    } catch (error) {
      alert('Save failed: ' + error.message);
    } finally {
      setIsSubmitting(false);
    }
  }, [name, age, sex, cvFile, isSubmitting]);

  return (
    <form onSubmit={submit} style={{padding:20}}>
      <h2 style={{color:'#00bcd4'}}>HustleX — Create Profile</h2>
      <label>Name</label><input value={name} onChange={e=>setName(e.target.value)} />
      <label>Age</label><input type="number" value={age} onChange={e=>setAge(e.target.value)} />
      <label>Sex</label>
      <select value={sex} onChange={e=>setSex(e.target.value)}>
        <option value="">Select</option><option value="male">Male</option><option value="female">Female</option><option value="other">Other</option>
      </select>
      <label>Upload CV (PDF)</label>
      <input type="file" accept=".pdf" onChange={e=>setCvFile(e.target.files[0])} />
      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? 'Saving...' : 'Save profile'}
      </button>
    </form>
  );
}
